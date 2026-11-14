"""HTTP conformance cases for any AI-oriented A2A v0.3 receiver.

The 12 HTTP test functions below are COPIED from
tests/interop/test_optional_fields.py ("HTTP handler behaviour"), together
with the ``_post`` and ``_assert_valid_rejection`` helpers. Test bodies are
unchanged. Only the ``start_receiver`` fixture differs: it lives
in conftest.py and is parametrized over implementations (the reference
``examples/network_dual_end.py``, ``second_impl``, and optionally an
external implementation).

DUPLICATION: until the two copies are merged (see tests/conformance/README.md
and docs/KNOWN_GAPS.md), a change to one copy MUST be made to the other.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:
    pytest.skip("jsonschema/referencing not installed", allow_module_level=True)

from ai_oriented_a2a import sign_message  # test-side signing only

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
SCHEMA_DIR = REPO / "schema"


def _local_registry():
    resources = []
    for path in SCHEMA_DIR.glob("*.schema.json"):
        contents = json.loads(path.read_text())
        resource = Resource.from_contents(contents)
        resources.append((path.name, resource))
        if contents.get("$id"):
            resources.append((contents["$id"], resource))
    return Registry().with_resources(resources)


REGISTRY = _local_registry()
RESULT_SCHEMA = json.loads((SCHEMA_DIR / "absorption-result.schema.json").read_text())
RESULT_V = Draft202012Validator(RESULT_SCHEMA, registry=REGISTRY)


def _valid_packet(**extra):
    base = json.loads((EXAMPLES / "experience-packet.json").read_text())
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Copied from tests/interop/test_optional_fields.py (keep in sync)
# ---------------------------------------------------------------------------


def _post(url, payload=None, raw=None, timeout=5):
    data = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/a2a/message",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = body
        return exc.code, parsed




def _assert_valid_rejection(status, body, reason_part, packet_ref=None):
    assert status == 200
    assert body["status"] == "rejected"
    assert isinstance(body["packet_ref"], str) and body["packet_ref"].strip()
    if packet_ref is not None:
        assert body["packet_ref"] == packet_ref
    assert reason_part in body["reason"]
    # Strip optional response signature before schema check if we want the
    # unsigned shape; with no sign_key there is no signature. When present,
    # the new schema accepts it.
    RESULT_V.validate(
        {
            k: body[k]
            for k in body
            if k in ("packet_ref", "status", "reason", "effect_summary",
                     "version", "nonce", "timestamp", "signature")
        }
    )


def test_http_unknown_version_returns_200_rejected(start_receiver):
    url = start_receiver()
    pkt = _valid_packet(version="0.4")
    status, body = _post(url, pkt)
    _assert_valid_rejection(status, body, "unsupported_version", pkt["id"])
    assert "0.4" in body["reason"]

    pkt2 = _valid_packet(version="v0.3")
    status, body = _post(url, pkt2)
    _assert_valid_rejection(status, body, "unsupported_version", pkt2["id"])
    assert "v0.3" in body["reason"]


def test_http_unknown_version_without_id_uses_reserved_ref(start_receiver):
    url = start_receiver()
    status, body = _post(url, {"version": "0.4", "constraints": []})
    _assert_valid_rejection(status, body, "unsupported_version", "unknown")


def test_http_signature_failure_returns_200_rejected(start_receiver):
    url = start_receiver(sign_key="secret")
    pkt = _valid_packet()
    # no signature at all
    status, body = _post(url, pkt)
    _assert_valid_rejection(status, body, "signature verification failed", pkt["id"])
    # bad signature
    bad = sign_message(pkt, "wrong")
    status, body = _post(url, bad)
    _assert_valid_rejection(status, body, "signature verification failed", pkt["id"])
    # good signature is not rejected for this reason (may absorb or reject on gate)
    good = sign_message(_valid_packet(), "secret")
    status, body = _post(url, good)
    assert status == 200
    assert body["status"] in ("absorbed", "rejected", "isolated")
    if body["status"] == "absorbed":
        assert body["packet_ref"] != "unknown"
        assert body["effect_summary"]["note"].strip()
        RESULT_V.validate({k: body[k] for k in body if k != "signature"} | (
            {"signature": body["signature"]} if "signature" in body else {}
        ))


def test_http_replay_only_when_window_enabled(start_receiver):
    pkt = _valid_packet(nonce="same-nonce", timestamp=int(time.time()))
    # Default: replay off, duplicate is not a replay rejection
    url_off = start_receiver()
    status1, body1 = _post(url_off, pkt)
    status2, body2 = _post(url_off, pkt)
    assert status1 == 200 and status2 == 200
    assert "replay_or_stale" not in body2.get("reason", "")

    url_on = start_receiver(replay_window=600)
    status1, body1 = _post(url_on, pkt)
    assert status1 == 200
    assert "replay_or_stale" not in body1.get("reason", "")
    status2, body2 = _post(url_on, pkt)
    _assert_valid_rejection(status2, body2, "replay_or_stale", pkt["id"])

    stale = _valid_packet(nonce="stale-n", timestamp=int(time.time()) - 10_000)
    status, body = _post(url_on, stale)
    _assert_valid_rejection(status, body, "replay_or_stale", stale["id"])


def test_http_replay_accepts_iso8601_timestamp(start_receiver):
    url = start_receiver(replay_window=600)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pkt = _valid_packet(nonce="iso-nonce", timestamp=now)
    status, body = _post(url, pkt)
    assert status == 200
    assert "replay_or_stale" not in body.get("reason", "")
    status, body = _post(url, pkt)
    _assert_valid_rejection(status, body, "replay_or_stale", pkt["id"])


def test_http_replay_window_rejects_missing_nonce_or_timestamp(start_receiver):
    url = start_receiver(replay_window=600)
    # both missing
    pkt = _valid_packet()
    assert "nonce" not in pkt and "timestamp" not in pkt
    status, body = _post(url, pkt)
    _assert_valid_rejection(status, body, "replay_or_stale", pkt["id"])
    # only nonce
    pkt_n = _valid_packet(nonce="only-n")
    assert "timestamp" not in pkt_n
    status, body = _post(url, pkt_n)
    _assert_valid_rejection(status, body, "replay_or_stale", pkt_n["id"])
    # only timestamp
    pkt_t = _valid_packet(timestamp=int(time.time()))
    assert "nonce" not in pkt_t
    status, body = _post(url, pkt_t)
    _assert_valid_rejection(status, body, "replay_or_stale", pkt_t["id"])
    # valid pair is accepted (not a replay rejection)
    pkt_ok = _valid_packet(nonce="both-present", timestamp=int(time.time()))
    status, body = _post(url, pkt_ok)
    assert status == 200
    assert "replay_or_stale" not in body.get("reason", "")


def test_http_replay_off_accepts_messages_without_nonce_or_timestamp(start_receiver):
    url = start_receiver()  # window off
    pkt = _valid_packet()
    assert "nonce" not in pkt and "timestamp" not in pkt
    status, body = _post(url, pkt)
    assert status == 200
    assert "replay_or_stale" not in body.get("reason", "")
    assert body["status"] in ("absorbed", "rejected", "isolated")
    # a second send without those fields is still not a replay rejection
    status2, body2 = _post(url, pkt)
    assert status2 == 200
    assert "replay_or_stale" not in body2.get("reason", "")


def test_http_sign_and_replay_independent(start_receiver):
    """Replay can be on without a sign key; sign can be on without a window."""
    url = start_receiver(sign_key="secret", replay_window=None)
    pkt = sign_message(_valid_packet(nonce="n", timestamp=int(time.time())), "secret")
    status, body = _post(url, pkt)
    assert status == 200
    # duplicate is allowed because replay is off
    status2, body2 = _post(url, pkt)
    assert status2 == 200
    assert "replay_or_stale" not in body2.get("reason", "")

    url2 = start_receiver(sign_key=None, replay_window=600)
    pkt2 = _valid_packet(nonce="only-replay", timestamp=int(time.time()))
    # unsigned is fine when no key was passed
    status, body = _post(url2, pkt2)
    assert status == 200
    assert "signature" not in body.get("reason", "")


def test_http_omitted_version_still_absorbed_or_valid_result(start_receiver):
    url = start_receiver()
    pkt = _valid_packet()
    assert "version" not in pkt
    status, body = _post(url, pkt)
    assert status == 200
    assert body["status"] in ("absorbed", "rejected", "isolated")
    assert body["packet_ref"] == pkt["id"]
    if body["status"] == "absorbed":
        assert body["effect_summary"]["note"].strip()
        assert body["packet_ref"] != "unknown"
    RESULT_V.validate({k: body[k] for k in ("packet_ref", "status", "reason", "effect_summary") if k in body})


def test_http_invalid_json_is_json_absorption_result_not_html(start_receiver):
    url = start_receiver()
    status, body = _post(url, raw=b"not-json{")
    assert status == 400
    assert isinstance(body, dict), f"expected JSON body, got {body!r}"
    assert body["packet_ref"] == "unknown"
    assert body["status"] == "rejected"
    assert "invalid json" in body["reason"]
    RESULT_V.validate(body)
    assert "<html" not in str(body).lower()


def test_http_incoming_hard_gate_violation_returns_200_rejected(start_receiver):
    url = start_receiver()
    bad = {"packet_ref": "pkt-experience-7f3a", "status": "absorbed"}
    status, body = _post(url, bad)
    _assert_valid_rejection(status, body, "hard-gate", "pkt-experience-7f3a")


def test_http_successful_absorption_never_uses_reserved_ref(start_receiver):
    url = start_receiver()
    # A well-formed packet with a real id
    pkt = _valid_packet()
    status, body = _post(url, pkt)
    assert status == 200
    if body["status"] == "absorbed":
        assert body["packet_ref"] != "unknown"
        assert body["packet_ref"] == pkt["id"]

    # A packet whose id is the reserved literal is not absorbed under that ref
    reserved = _valid_packet()
    reserved["id"] = "unknown"
    status, body = _post(url, reserved)
    assert status == 200
    if body["status"] == "absorbed":
        raise AssertionError("must not absorb with reserved packet_ref 'unknown'")
    assert body["packet_ref"] == "unknown"
    assert body["status"] == "rejected"
