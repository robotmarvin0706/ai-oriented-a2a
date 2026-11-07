#!/usr/bin/env python3
"""
Minimal network dual-endpoint reference (non-normative).

Two processes exchange Agent Profile, Experience Packet, Absorption Result
over HTTP using only the Python standard library.

For supported absorption methods the receiver runs the local regression +
isolation N path from minimal_dual_end.Receiver.absorb (experiment-path hard
gate). Unsupported methods are rejected explicitly. effect_summary remains
OPTIONAL on the wire.

Optional signature (HMAC-SHA256) via --sign-key.
Optional replay protection: messages MAY carry nonce (opaque) + timestamp
(unix seconds). Receivers keep a short sliding window of seen nonces and
reject duplicates or messages outside the window with reason replay_or_stale.

Does not change wire shapes required by SPEC.md.

Usage:
  # Terminal 1 (receiver)
  python3 examples/network_dual_end.py --role receiver --port 8002 --sign-key demo

  # Terminal 2 (sender)
  python3 examples/network_dual_end.py --role sender --port 8001 --peer http://127.0.0.1:8002 --sign-key demo
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import random
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

_EXAMPLES = Path(__file__).resolve().parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
from minimal_dual_end import MUST_METHODS, Receiver  # noqa: E402

REPLAY_WINDOW_SECONDS = 10 * 60  # 10 minutes, per SECURITY.md suggestion


def new_id(prefix: str = "pkt") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def canonical(obj: Dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


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
    return hmac.compare_digest(expected, sig_obj.get("value", ""))


def check_replay(msg: Dict[str, Any], seen: Set[Tuple[str, str]]) -> Optional[str]:
    """Return reason string if replay/stale, else None. Mutates seen on accept."""
    nonce = msg.get("nonce")
    ts = msg.get("timestamp")
    if nonce is None and ts is None:
        return None  # optional; absence is not a violation
    if not isinstance(nonce, str) or not nonce:
        return "replay_or_stale: missing or empty nonce"
    if not isinstance(ts, (int, float)):
        return "replay_or_stale: missing or non-numeric timestamp"
    now = time.time()
    if abs(now - float(ts)) > REPLAY_WINDOW_SECONDS:
        return "replay_or_stale: timestamp outside window"
    key = (nonce, str(int(ts)))
    if key in seen:
        return "replay_or_stale: duplicate nonce"
    seen.add(key)
    # Opportunistic cleanup of old entries (demo only).
    if len(seen) > 1000:
        cutoff = now - REPLAY_WINDOW_SECONDS
        # We don't store timestamps separately in this minimal set; just trim.
        # Real impl would keep (nonce, ts) pairs with proper expiry.
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


class A2AHandler(BaseHTTPRequestHandler):
    role: str = "receiver"
    peer_url: Optional[str] = None
    sign_key: Optional[str] = None
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
        except Exception:
            self.send_error(400, "invalid json")
            return

        if self.sign_key:
            if not verify_message(msg, self.sign_key):
                result = {"status": "rejected", "reason": "signature verification failed"}
                self._reply(result)
                return

        replay_reason = check_replay(msg, self.seen_nonces)
        if replay_reason:
            result = {
                "packet_ref": msg.get("id") or msg.get("packet_ref") or "",
                "status": "rejected",
                "reason": replay_reason,
            }
            self._reply(result)
            return

        print(f"[{self.role}] received: {json.dumps(msg, ensure_ascii=False)[:180]}...")
        self.received.append(msg)

        response: Dict[str, Any] = {"ack": True}

        if "constraints" in msg:
            response["type"] = "profile_ack"
        elif "id" in msg and "absorption" in msg:
            method = msg.get("absorption", {}).get("method")
            if method not in MUST_METHODS and method not in ("local_adapter_hint", "trajectory_replay"):
                result = {
                    "packet_ref": msg["id"],
                    "status": "rejected",
                    "reason": f"unsupported method: {method}",
                }
            else:
                rng = random.Random(hash(msg["id"]) & 0xFFFFFFFF)
                result = self.receiver.absorb(msg, rng)
            response = result
            if self.role == "receiver" and self.peer_url:
                threading.Thread(target=self._post_to_peer, args=(result,), daemon=True).start()
        elif "packet_ref" in msg:
            response["type"] = "result_ack"

        self._reply(response)

    def _reply(self, payload: Dict[str, Any]) -> None:
        if self.sign_key:
            payload = sign_message(payload, self.sign_key)
        data = json.dumps(payload).encode()
        self.send_response(200)
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
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(f"[{self.role}] pushed result, status={resp.status}")
        except Exception as e:
            print(f"[{self.role}] push failed: {e}")


def run_server(port: int, role: str, peer_url: Optional[str], sign_key: Optional[str]) -> None:
    A2AHandler.role = role
    A2AHandler.peer_url = peer_url
    A2AHandler.sign_key = sign_key
    server = HTTPServer(("127.0.0.1", port), A2AHandler)
    print(f"[{role}] listening on http://127.0.0.1:{port}/a2a/message (sign={'on' if sign_key else 'off'})")
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
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["sender", "receiver"], required=True)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--peer", type=str, default=None, help="peer base URL")
    parser.add_argument("--sign-key", type=str, default=None, help="optional HMAC key; enables signature")
    args = parser.parse_args()

    if args.role == "receiver":
        run_server(args.port, "receiver", args.peer, args.sign_key)
    else:
        threading.Thread(
            target=run_server, args=(args.port, "sender", args.peer, args.sign_key), daemon=True
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
