#!/usr/bin/env python3
"""
Minimal network dual-endpoint reference (non-normative).

Two processes exchange Agent Profile, Experience Packet, Absorption Result
over HTTP using only the Python standard library.

Core helpers (canonical JSON, HMAC, replay window, version rejection,
legal rejected Absorption Results) live in the installable package
``ai_oriented_a2a``. This file remains the HTTP dual-endpoint demo.

For supported absorption methods the receiver runs the local regression +
isolation N path from minimal_dual_end.Receiver.absorb (experiment-path gate,
stricter than the v0.3 wire hard gate). Unsupported methods are rejected
explicitly. On the wire, effect_summary.note is MUST when status is absorbed
(SPEC §3.1); full metrics remain OPTIONAL. The receiver converts a missing
or non-compliant summary to rejected rather than emitting an invalid absorbed
result.

Optional signature (HMAC-SHA256) via --sign-key. Passing a key enables
verification; omitting it leaves verification off. Optional replay protection
via --replay-window SECONDS: when passed, messages MUST carry nonce (opaque)
and timestamp (unix seconds or ISO-8601). The receiver keeps a sliding window
of seen nonces and rejects missing fields, duplicates, or messages outside
the window with reason replay_or_stale. When the window is not passed,
nonce and timestamp remain optional and their absence is not a violation.
The two checks are independent. Default (neither flag) leaves both off,
so old peers still work.

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
import json
import random
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

_EXAMPLES = Path(__file__).resolve().parent
_REPO = _EXAMPLES.parent
# Allow `python3 examples/network_dual_end.py` without installing the package.
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
from ai_oriented_a2a import (  # noqa: E402
    RESERVED_PACKET_REF,
    SUPPORTED_MAJOR_MINOR,
    VERSION_PATTERN,
    canonical,
    check_incoming,
    check_replay,
    new_id,
    packet_ref_of,
    parse_timestamp,
    reject_result,
    sign_message,
    supported_version,
    verify_message,
    version_rejection,
)
from minimal_dual_end import MUST_METHODS, Receiver  # noqa: E402

DEFAULT_REPLAY_WINDOW_SECONDS = 10 * 60  # 10 minutes, per SECURITY.md suggestion
CLIENT_TIMEOUT_SECONDS = 30  # TRANSPORT.md: library SHOULD use >= 30s


def make_profile() -> Dict[str, Any]:
    return {
        "constraints": ["web_search allowed", "no parameter_delta"],
        "improvement_prefs": ["failure_mode", "tool_trajectory"],
        "shareable_types": ["failure_mode"],
        "interest_tags": ["timeout_recovery"],
        "nonce": new_id("nonce"),
        "timestamp": int(time.time()),
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

        rejected = check_incoming(
            msg,
            sign_key=self.sign_key,
            replay_window=self.replay_window,
            seen=self.seen_nonces,
        )
        if rejected:
            self._reply(rejected)
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
        # Attach replay fields before signing so a peer with the window
        # enabled can accept the pushed Absorption Result.
        if payload.get("nonce") is None:
            payload = dict(payload)
            payload["nonce"] = new_id("nonce")
        if payload.get("timestamp") is None:
            payload = dict(payload)
            payload["timestamp"] = int(time.time())
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
            "optional replay window in seconds; passing this requires nonce "
            "and timestamp on incoming messages (suggested 600 for a 10-minute window)"
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
