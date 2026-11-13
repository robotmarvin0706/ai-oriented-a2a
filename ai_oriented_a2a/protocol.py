"""Stdlib helpers for the AI-oriented A2A peer protocol (v0.3).

No mandatory signature or replay checks, no HTTP server, and no
central components. Signature and replay stay off unless the caller
passes ``sign_key`` or ``replay_window``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, Tuple

RESERVED_PACKET_REF = "unknown"
VERSION_PATTERN = re.compile(r"^\d+\.\d+(\.\d+)?$")
SUPPORTED_MAJOR_MINOR = (0, 3)


def new_id(prefix: str = "pkt") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def canonical(obj: Dict[str, Any]) -> bytes:
    """Return canonical JSON bytes used for HMAC-SHA256 signatures.

    The canonical form is the UTF-8 encoding of
    ``json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False)``.
    The caller MUST pass an object that already excludes the ``signature``
    field; other fields (including ``version``, ``nonce``, and ``timestamp``)
    are included. ``sort_keys=True`` applies recursively. Integer Unix
    seconds are recommended for ``timestamp`` so float formatting does not
    affect the digest.
    """
    return json.dumps(
        obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")


def sign_message(msg: Dict[str, Any], key: str) -> Dict[str, Any]:
    body = {k: v for k, v in msg.items() if k != "signature"}
    sig = hmac.new(key.encode(), canonical(body), hashlib.sha256).hexdigest()
    out = dict(msg)
    out["signature"] = {"alg": "hmac-sha256", "value": sig}
    return out


def verify_message(msg: Dict[str, Any], key: str) -> bool:
    sig_obj = msg.get("signature")
    if not isinstance(sig_obj, dict) or sig_obj.get("alg") != "hmac-sha256":
        return False
    body = {k: v for k, v in msg.items() if k != "signature"}
    expected = hmac.new(key.encode(), canonical(body), hashlib.sha256).hexdigest()
    value = sig_obj.get("value", "")
    if not isinstance(value, str):
        return False
    return hmac.compare_digest(expected, value)


def packet_ref_of(msg: Optional[Dict[str, Any]]) -> str:
    """Usable packet_ref for an Absorption Result, or the reserved literal."""
    if not msg:
        return RESERVED_PACKET_REF
    for key in ("id", "packet_ref"):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return RESERVED_PACKET_REF


def reject_result(msg: Optional[Dict[str, Any]], reason: str) -> Dict[str, Any]:
    return {
        "packet_ref": packet_ref_of(msg),
        "status": "rejected",
        "reason": reason,
    }


def parse_timestamp(ts: Any) -> Optional[float]:
    """Parse a unix-seconds number or an ISO-8601 string into epoch seconds.

    Naive (no timezone) strings are treated as UTC. A trailing ``Z`` is
    accepted. Returns None if the value cannot be parsed.
    """
    if isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts.strip():
        text = ts.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def supported_version(value: str) -> bool:
    """True if an on-wire version string is the current 0.3 minor line."""
    if not VERSION_PATTERN.match(value):
        return False
    parts = value.split(".")
    return (int(parts[0]), int(parts[1])) == SUPPORTED_MAJOR_MINOR


def version_rejection(msg: Dict[str, Any]) -> Optional[str]:
    """Return an unsupported_version reason, or None if the message is fine.

    Omitted version is treated as the current normative version (0.3).
    A leading ``v`` or any other pattern failure, and any recognized-pattern
    value that is not the 0.3 line, MUST be rejected.
    """
    if "version" not in msg:
        return None
    raw = msg["version"]
    display = raw if isinstance(raw, str) else repr(raw)
    if not isinstance(raw, str) or not supported_version(raw):
        return f"unsupported_version: received {display}"
    return None


def check_replay(
    msg: Dict[str, Any],
    seen: Set[Tuple[str, str]],
    window_seconds: float,
) -> Optional[str]:
    """Return reason string if replay/stale, else None. Mutates seen on accept.

    Called only when a replay window has been enabled.
    Both nonce and timestamp are required when the window is on.
    """
    nonce = msg.get("nonce")
    ts = msg.get("timestamp")
    if nonce is None or ts is None:
        return "replay_or_stale: nonce and timestamp required when replay window is enabled"
    if not isinstance(nonce, str) or not nonce:
        return "replay_or_stale: missing or empty nonce"
    parsed = parse_timestamp(ts)
    if parsed is None:
        return "replay_or_stale: missing or unparsable timestamp"
    now = time.time()
    if abs(now - parsed) > window_seconds:
        return "replay_or_stale: timestamp outside window"
    key = (nonce, str(int(parsed)))
    if key in seen:
        return "replay_or_stale: duplicate nonce"
    seen.add(key)
    if len(seen) > 1000:
        while len(seen) > 800:
            seen.pop()
    return None


def check_incoming(
    msg: Dict[str, Any],
    sign_key: Optional[str] = None,
    replay_window: Optional[float] = None,
    seen: Optional[Set[Tuple[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Return a rejected Absorption Result if checks fail, else None.

    Check order is fixed: version first, then signature (only if
    ``sign_key`` is passed), then replay (only if ``replay_window`` is
    passed). Default leaves signature and replay off. When a replay
    window is enabled, both ``nonce`` and ``timestamp`` are required.
    The reserved packet_ref ``"unknown"`` is used for early failures
    that have no usable id; it MUST NOT be used for a successful
    absorption.

    ``seen`` is the sliding window of ``(nonce, timestamp)`` keys. The
    caller MUST pass and reuse the same set across calls when replay
    is on; if it is omitted, a fresh empty set is used for this call
    only (duplicate detection will not persist).
    """
    version_reason = version_rejection(msg)
    if version_reason:
        return reject_result(msg, version_reason)
    if sign_key:
        if not verify_message(msg, sign_key):
            return reject_result(msg, "signature verification failed")
    if replay_window is not None:
        if seen is None:
            seen = set()
        replay_reason = check_replay(msg, seen, replay_window)
        if replay_reason:
            return reject_result(msg, replay_reason)
    return None
