"""Stdlib helpers for the AI-oriented A2A peer protocol (v0.3).

No mandatory signature or replay checks and no central components (the
HTTP binding lives in ``ai_oriented_a2a.transport``). Signature and replay stay off unless the caller
passes ``sign_key`` or ``replay_window``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import threading
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, ContextManager, Dict, Optional, Set, Tuple, Union

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

    Naive (no timezone) strings are treated as UTC. This is compatibility
    behaviour only: the published schemas require a timezone, and strict
    receivers MAY reject such strings with ``schema_invalid:``. A trailing
    ``Z`` is accepted. Returns None if the value cannot be parsed, including
    non-finite numbers (``NaN``, ``Infinity``, ``-Infinity``; ``json.loads``
    accepts them) and integers too large for a float.
    """
    if isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        try:
            value = float(ts)
        except OverflowError:
            return None
        return value if math.isfinite(value) else None
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


# Replay cache bounds. ``seen`` stays a plain ``set`` of ``(nonce, str(int(ts)))``
# keys; when it grows past SEEN_MAX it is trimmed to SEEN_TRIM_TO, dropping
# expired keys first and then the oldest timestamps (never arbitrary keys).
SEEN_MAX = 1000
SEEN_TRIM_TO = 800

INVALID_TIMESTAMP_REASON = "replay_or_stale: invalid timestamp"

SeenSet = Set[Tuple[str, str]]


class ReplayCache:
    """A replay ``seen`` set bound to the ``threading.Lock`` that guards it.

    Share one instance between every thread that checks messages for the same
    receiver (for example all request threads of one ``ThreadingHTTPServer``).
    The duplicate check, the insert, and the eviction all run under ``lock``.
    ``seen`` may be an existing set; it is used as is, not copied.
    """

    __slots__ = ("seen", "lock")

    def __init__(self, seen: Optional[SeenSet] = None) -> None:
        self.seen: SeenSet = set() if seen is None else seen
        self.lock = threading.Lock()

    def __len__(self) -> int:
        return len(self.seen)


def _key_ts(key: Tuple[str, str]) -> float:
    try:
        return float(key[1])
    except (TypeError, ValueError, IndexError):
        return float("-inf")


def _evict(seen: SeenSet, now: float, window_seconds: float) -> None:
    """Trim ``seen`` to SEEN_TRIM_TO: expired keys first, then oldest timestamp.

    Expired keys can never be accepted again (the timestamp check runs
    first), so dropping them is free. Dropping a key that is still inside
    the window means that nonce could be replayed; keep the window short
    if that matters (docs/SECURITY.md). The caller holds the cache lock.
    """
    for key in [k for k in seen if abs(now - _key_ts(k)) > window_seconds]:
        seen.discard(key)
    excess = len(seen) - SEEN_TRIM_TO
    if excess > 0:
        for key in sorted(seen, key=lambda k: (_key_ts(k), k))[:excess]:
            seen.discard(key)


def _is_non_finite_number(ts: Any) -> bool:
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False
    try:
        return not math.isfinite(float(ts))
    except OverflowError:
        return True


def _seen_and_guard(
    seen: Union[SeenSet, ReplayCache], lock: Optional[Any]
) -> Tuple[SeenSet, ContextManager[Any]]:
    if isinstance(seen, ReplayCache):
        return seen.seen, seen.lock
    return seen, (lock if lock is not None else nullcontext())


def check_replay(
    msg: Dict[str, Any],
    seen: Union[SeenSet, ReplayCache],
    window_seconds: float,
    lock: Optional[Any] = None,
) -> Optional[str]:
    """Return reason string if replay/stale, else None. Mutates seen on accept.

    Called only when a replay window has been enabled.
    Both nonce and timestamp are required when the window is on.

    A nonce is a duplicate if it is already in ``seen``, whatever its
    timestamp (the same nonce with a different timestamp is rejected).
    ``seen`` is a plain set of ``(nonce, str(int(timestamp)))`` keys, or a
    ``ReplayCache`` that bundles that set with its lock; when it exceeds
    SEEN_MAX it is trimmed to SEEN_TRIM_TO by dropping expired keys first and
    then the oldest timestamps.

    Non-finite numeric timestamps (``NaN``, ``Infinity``, ``-Infinity``) are
    rejected with ``replay_or_stale: invalid timestamp``.

    Thread safety: the duplicate check, insert, and eviction run under one
    lock. Pass a ``ReplayCache`` (its own lock is used), or a bare set plus
    ``lock``. A bare set without ``lock`` is unguarded: that is fine for a
    single thread, but threads sharing one set MUST share one lock too, or
    concurrent requests can fail with ``Set changed size during iteration``.
    """
    nonce = msg.get("nonce")
    ts = msg.get("timestamp")
    if nonce is None or ts is None:
        return "replay_or_stale: nonce and timestamp required when replay window is enabled"
    if not isinstance(nonce, str) or not nonce:
        return "replay_or_stale: missing or empty nonce"
    if _is_non_finite_number(ts):
        return INVALID_TIMESTAMP_REASON
    parsed = parse_timestamp(ts)
    if parsed is None:
        return "replay_or_stale: missing or unparsable timestamp"
    now = time.time()
    if abs(now - parsed) > window_seconds:
        return "replay_or_stale: timestamp outside window"
    keys, guard = _seen_and_guard(seen, lock)
    with guard:
        if any(key[0] == nonce for key in keys):
            return "replay_or_stale: duplicate nonce"
        keys.add((nonce, str(int(parsed))))
        if len(keys) > SEEN_MAX:
            _evict(keys, now, window_seconds)
    return None


def check_incoming(
    msg: Dict[str, Any],
    sign_key: Optional[str] = None,
    replay_window: Optional[float] = None,
    seen: Optional[Union[SeenSet, ReplayCache]] = None,
    lock: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Return a rejected Absorption Result if checks fail, else None.

    Check order is fixed: version first, then signature (only if
    ``sign_key`` is passed), then replay (only if ``replay_window`` is
    passed). Default leaves signature and replay off. When a replay
    window is enabled, both ``nonce`` and ``timestamp`` are required.
    The reserved packet_ref ``"unknown"`` is used for early failures
    that have no usable id; it MUST NOT be used for a successful
    absorption.

    ``seen`` is the sliding window of ``(nonce, timestamp)`` keys: a plain
    set or a ``ReplayCache``. The caller MUST pass and reuse the same one
    across calls when replay is on; if it is omitted, a fresh empty set is
    used for this call only (duplicate detection will not persist). When
    several threads share it, pass a ``ReplayCache`` (or a bare set plus one
    shared ``lock``); see ``check_replay``.
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
        replay_reason = check_replay(msg, seen, replay_window, lock=lock)
        if replay_reason:
            return reject_result(msg, replay_reason)
    return None
