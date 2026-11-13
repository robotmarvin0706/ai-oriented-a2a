"""Installable package surface and check_incoming behaviour."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
EXAMPLES = REPO / "examples"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

import ai_oriented_a2a  # noqa: E402
from ai_oriented_a2a import (  # noqa: E402
    RESERVED_PACKET_REF,
    SCHEMA_KINDS,
    __all__,
    __version__,
    canonical,
    check_incoming,
    schema_dir,
    sign_message,
    validate,
    verify_message,
)
import network_dual_end  # noqa: E402


EXPECTED_EXPORTS = {
    "RESERVED_PACKET_REF",
    "SCHEMA_KINDS",
    "SUPPORTED_MAJOR_MINOR",
    "VERSION_PATTERN",
    "SchemaDependencyError",
    "__version__",
    "canonical",
    "check_incoming",
    "check_replay",
    "new_id",
    "packet_ref_of",
    "parse_timestamp",
    "reject_result",
    "schema_dir",
    "sign_message",
    "supported_version",
    "validate",
    "verify_message",
    "version_rejection",
}

SHARED_FUNCTIONS = (
    "canonical",
    "check_replay",
    "new_id",
    "packet_ref_of",
    "parse_timestamp",
    "reject_result",
    "sign_message",
    "supported_version",
    "verify_message",
    "version_rejection",
)


def test_version_and_all():
    assert __version__ == "0.3.0"
    assert ai_oriented_a2a.__version__ == "0.3.0"
    assert set(__all__) == EXPECTED_EXPORTS
    for name in __all__:
        assert hasattr(ai_oriented_a2a, name)


def test_example_and_package_are_the_same_functions():
    for name in SHARED_FUNCTIONS:
        assert getattr(network_dual_end, name) is getattr(ai_oriented_a2a, name)
    assert network_dual_end.RESERVED_PACKET_REF == RESERVED_PACKET_REF == "unknown"
    assert network_dual_end.VERSION_PATTERN is ai_oriented_a2a.VERSION_PATTERN
    assert network_dual_end.SUPPORTED_MAJOR_MINOR == ai_oriented_a2a.SUPPORTED_MAJOR_MINOR


def test_check_incoming_default_off():
    # No version, nonce, timestamp, or signature: checks pass.
    msg = {"id": "pkt-1", "type": "failure_mode"}
    assert check_incoming(msg) is None
    # Unsigned messages are accepted when sign_key is omitted,
    # even if a signature field is present and wrong.
    msg_with_junk_sig = {
        "id": "pkt-2",
        "signature": {"alg": "hmac-sha256", "value": "deadbeef"},
    }
    assert check_incoming(msg_with_junk_sig) is None
    # Replay fields may be omitted when the window is off.
    assert check_incoming({"id": "pkt-3"}, replay_window=None) is None
    # Default does still reject an unknown version.
    rejected = check_incoming({"id": "pkt-4", "version": "v0.3"})
    assert rejected is not None
    assert rejected["status"] == "rejected"
    assert "unsupported_version" in rejected["reason"]
    assert rejected["packet_ref"] == "pkt-4"


def test_check_incoming_version_is_checked_first():
    # Bad version, bad signature, and missing replay fields: the reason
    # must be unsupported_version, not signature or replay_or_stale.
    msg = {
        "id": "pkt-order",
        "version": "v0.3",
        "signature": {"alg": "hmac-sha256", "value": "nope"},
    }
    seen = set()
    rejected = check_incoming(
        msg, sign_key="secret", replay_window=600, seen=seen
    )
    assert rejected is not None
    assert rejected["status"] == "rejected"
    assert rejected["reason"].startswith("unsupported_version")
    assert "signature" not in rejected["reason"]
    assert "replay_or_stale" not in rejected["reason"]
    assert seen == set()  # replay was not reached


def test_check_incoming_opt_in_signature_and_replay():
    body = {"id": "pkt-ok", "version": "0.3", "nonce": "n1", "timestamp": int(time.time())}
    signed = sign_message(body, "secret")
    seen = set()
    assert check_incoming(signed, sign_key="secret", replay_window=600, seen=seen) is None
    # Duplicate nonce is rejected once the window is on.
    dup = check_incoming(signed, sign_key="secret", replay_window=600, seen=seen)
    assert dup is not None
    assert "duplicate nonce" in dup["reason"]
    # Wrong key is a signature failure (version is fine, so this is next).
    bad = check_incoming(signed, sign_key="other")
    assert bad is not None
    assert bad["reason"] == "signature verification failed"
    # Window on, fields missing.
    missing = check_incoming({"id": "pkt-x", "version": "0.3"}, replay_window=600, seen=set())
    assert missing is not None
    assert "nonce and timestamp required" in missing["reason"]


def test_check_incoming_reserved_packet_ref_on_early_failure():
    rejected = check_incoming({"version": "v0.3"})
    assert rejected["packet_ref"] == RESERVED_PACKET_REF == "unknown"
    assert rejected["status"] == "rejected"


def test_canonical_form_is_unchanged():
    body = {"b": 1, "a": 2, "note": "回归通过"}
    expected = json.dumps(
        body, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    assert canonical(body) == expected
    signed = sign_message(dict(body), "secret")
    assert "signature" in signed
    assert verify_message(signed, "secret")
    assert not verify_message(signed, "other")


def test_schema_dir_and_optional_validate():
    directory = schema_dir()
    assert directory.is_dir()
    for filename in SCHEMA_KINDS.values():
        assert (directory / filename).is_file()

    packet = json.loads((EXAMPLES / "experience-packet.json").read_text())
    validate(packet, "experience-packet")

    try:
        validate({"status": "absorbed"}, "absorption-result")
        raise AssertionError("expected validation to fail for absorbed without note")
    except Exception as exc:
        # jsonschema.ValidationError — do not import at module top so this
        # file can still be collected if only the core package is installed.
        assert type(exc).__name__ == "ValidationError"

    try:
        validate({}, "not-a-kind")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Unknown schema kind" in str(exc)
