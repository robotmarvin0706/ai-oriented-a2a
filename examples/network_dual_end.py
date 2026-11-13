#!/usr/bin/env python3
"""
Minimal network dual-endpoint reference (non-normative).

Two processes exchange Agent Profile, Experience Packet, Absorption Result
over HTTP using only the Python standard library.

For supported absorption methods the receiver runs the local regression +
isolation N path from minimal_dual_end.Receiver.absorb (experiment-path gate,
stricter than the v0.3 wire hard gate). Unsupported methods are rejected
explicitly. On the wire, effect_summary.note is MUST when status is absorbed
(SPEC §3.1); full metrics remain OPTIONAL. The receiver converts a missing
or non-compliant summary to rejected rather than emitting an invalid absorbed
result.

Optional signature (HMAC-SHA256) via --sign-key. Passing a key enables
verification; omitting it leaves verification off. Optional replay protection
via --replay-window SECONDS: when passed, messages MAY carry nonce (opaque)
and timestamp (unix seconds or ISO-8601). The receiver keeps a sliding window
of seen nonces and rejects duplicates or messages outside the window with
reason replay_or_stale. The two checks are independent. Default (neither
flag) leaves both off, so old peers still work.

Signature covers the canonical JSON of the message excluding the signature
field itself (see canonical()).

On signature failure, replay rejection, unknown version, or a hard-gate
failure converted to rejected, the handler returns HTTP 200 and a valid
Absorption Result with a non-empty packet_ref and reason. If the request has
no usable id, packet_ref is the reserved literal "unknown", which MUST NOT
be used as the packet_ref of a successful absorption.

First-edition library boundary: HTTP binding (POST /a2a/message), absorption
hard gate, legal error responses, optional HMAC and replay window. Redaction
and provenance implementation are out of scope (docs first).

HTTP binding details: docs/TRANSPORT.md.

Usage:
  # Terminal 1 (receiver)
  python3 examples/network_dual_end.py --role receiver --port 8002 --sign-key demo --replay-window 600

  # Terminal 2 (sender)
  python3 examples/network_dual_end.py --role sender --port 8001 --peer http://127.0.0.1:8002 --sign-key demo --replay-window 600
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import random
import re
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

_EXAMPLES = Path(__file__).resolve().parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
from minimal_dual_end import MUST_METHODS, Receiver  # noqa: E402

DEFAULT_REPLAY_WINDOW_SECONDS = 10 * 60  # 10 minutes, per SECURITY.md suggestion
CLIENT_TIMEOUT_SECONDS = 30  # TRANSPORT.md: library SHOULD use >= 30s
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

    Called only when a replay window has been enabled. Absence of both
    nonce and timestamp is still not a protocol violation.
    """
    nonce = msg.get("nonce")
    ts = msg.get("timestamp")
    if nonce is None and ts is None:
        return None  # optional; absence is not a violation
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


def make_profile() -> Dict[str, Any]:
    return {
        "constraints": ["web_search allowed", "no parameter_delta"],
        "improvement_prefs": ["failure_mode", "tool_trajectory"],
        "shareable_types": ["failure_mode"],
        "interest_tags": ["timeout_recovery"],
    }


def make_packet(method: str = "prompt_injection") -> Dict[str, Any]:
    return {
        "id": new_id(),
        "type": "failure_mode",
        "content": {
            "trigger": "tool_call_timeout on web_search",
            "observed": "retry_loop without backoff",
            "compressed_signal": "apply exponential backoff and drop results >30s old",
        },
        "absorption": {
            "method": method,
            "target": "system_or_tool_instruction",
            "hint": "prepend recovery instruction",
        },
        "expiry": {"condition": "after_local_absorption_or_24h"},
        "nonce": new_id("nonce"),
        "timestamp": int(time.time()),
    }


def guard_reserved_ref(result: Dict[str, Any]) -> Dict[str, Any]:
    """Refuse to emit status=absorbed with the reserved packet_ref."""
    if result.get("status") == "absorbed" and result.get("packet_ref") == RESERVED_PACKET_REF:
        return reject_result(
            result,
            "reserved packet_ref 'unknown' cannot be used for successful absorption",
        )
    return result


class A2AHandler(BaseHTTPRequestHandler):
    role: str = "receiver"
    peer_url: Optional[str] = None
    sign_key: Optional[str] = None
    replay_window: Optional[float] = None  # seconds; None = replay checks off
    received: list = []
    receiver: Receiver = Receiver(isolation_n=3, regression_drop_threshold=0.10)
    # Process-level seen nonces for the demo (per-peer would key by identity/fingerprint).
    seen_nonces: Set[Tuple[str, str]] = set()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.role}] {self.address_string()} - {format % args}")

    def do_POST(self) -> None:
        if self.path != "/a2a/message":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            msg = json.loads(body.decode())
            if not isinstance(msg, dict):
                raise ValueError("message must be a JSON object")
        except Exception:
            self._reply(reject_result(None, "invalid json"), status=400)
            return

        version_reason = version_rejection(msg)
        if version_reason:
            self._reply(reject_result(msg, version_reason))
            return

        if self.sign_key:
            if not verify_message(msg, self.sign_key):
                self._reply(reject_result(msg, "signature verification failed"))
                return

        if self.replay_window is not None:
            replay_reason = check_replay(msg, self.seen_nonces, self.replay_window)
            if replay_reason:
                self._reply(reject_result(msg, replay_reason))
                return

        print(f"[{self.role}] received: {json.dumps(msg, ensure_ascii=False)[:180]}...")
        self.received.append(msg)

        response: Dict[str, Any] = {"ack": True}

        if "constraints" in msg:
            response["type"] = "profile_ack"
        elif "id" in msg and "absorption" in msg:
            method = msg.get("absorption", {}).get("method")
            if method not in MUST_METHODS and method not in ("local_adapter_hint", "trajectory_replay"):
                result = reject_result(msg, f"unsupported method: {method}")
            else:
                rng = random.Random(hash(msg["id"]) & 0xFFFFFFFF)
                result = self.receiver.absorb(msg, rng)
            response = guard_reserved_ref(result)
            if self.role == "receiver" and self.peer_url:
                threading.Thread(target=self._post_to_peer, args=(response,), daemon=True).start()
        elif "packet_ref" in msg:
            # Received Absorption Result. Hard-gate violations are a protocol
            # violation at the peer: we still return HTTP 200 and a valid
            # Absorption Result rather than an HTML or empty body, but we do
            # not silently absorb the claim.
            if msg.get("status") == "absorbed":
                note = ""
                summary = msg.get("effect_summary")
                if isinstance(summary, dict):
                    note = summary.get("note") or ""
                if not (isinstance(note, str) and note.strip()):
                    response = reject_result(
                        msg,
                        "hard-gate violation: absorbed result missing non-empty effect_summary.note",
                    )
                else:
                    response["type"] = "result_ack"
            else:
                response["type"] = "result_ack"

        self._reply(response)

    def _reply(self, payload: Dict[str, Any], status: int = 200) -> None:
        if self.sign_key:
            payload = sign_message(payload, self.sign_key)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _post_to_peer(self, payload: Dict[str, Any]) -> None:
        if not self.peer_url:
            return
        if self.sign_key:
            payload = sign_message(payload, self.sign_key)
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.peer_url.rstrip("/") + "/a2a/message",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=CLIENT_TIMEOUT_SECONDS) as resp:
                print(f"[{self.role}] pushed result, status={resp.status}")
        except Exception as e:
            print(f"[{self.role}] push failed: {e}")


def run_server(
    port: int,
    role: str,
    peer_url: Optional[str],
    sign_key: Optional[str],
    replay_window: Optional[float] = None,
) -> None:
    A2AHandler.role = role
    A2AHandler.peer_url = peer_url
    A2AHandler.sign_key = sign_key
    A2AHandler.replay_window = replay_window
    A2AHandler.seen_nonces = set()
    server = HTTPServer(("127.0.0.1", port), A2AHandler)
    window_desc = f"{replay_window}s" if replay_window is not None else "off"
    print(
        f"[{role}] listening on http://127.0.0.1:{port}/a2a/message "
        f"(sign={'on' if sign_key else 'off'}, replay={window_desc})"
    )
    server.serve_forever()


def send_message(url: str, payload: Dict[str, Any], sign_key: Optional[str] = None) -> Dict[str, Any]:
    if sign_key:
        payload = sign_message(payload, sign_key)
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/a2a/message",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=CLIENT_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["sender", "receiver"], required=True)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--peer", type=str, default=None, help="peer base URL")
    parser.add_argument(
        "--sign-key",
        type=str,
        default=None,
        help="optional HMAC key; passing this enables signature verification",
    )
    parser.add_argument(
        "--replay-window",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "optional replay window in seconds; passing this enables nonce/"
            "timestamp checks (suggested 600 for a 10-minute window)"
        ),
    )
    args = parser.parse_args()

    if args.role == "receiver":
        run_server(args.port, "receiver", args.peer, args.sign_key, args.replay_window)
    else:
        threading.Thread(
            target=run_server,
            args=(args.port, "sender", args.peer, args.sign_key, args.replay_window),
            daemon=True,
        ).start()
        time.sleep(0.5)
        if not args.peer:
            print("sender requires --peer")
            return
        print("[sender] sending profile...")
        print(send_message(args.peer, make_profile(), args.sign_key))
        time.sleep(0.3)
        pkt = make_packet()
        print(f"[sender] sending packet {pkt['id']}...")
        result = send_message(args.peer, pkt, args.sign_key)
        print("[sender] absorption result:", json.dumps(result, indent=2, ensure_ascii=False))
        print("[sender] done. Ctrl-C to exit.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
