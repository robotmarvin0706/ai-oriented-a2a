#!/usr/bin/env python3
"""
Minimal network dual-endpoint reference (non-normative).

Two processes exchange Agent Profile, Experience Packet, Absorption Result
over HTTP using only the Python standard library.

Optional signature (HMAC-SHA256) via --sign-key. Does not change wire shapes
required by SPEC.md; signature is an optional top-level field.

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
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

MUST_METHODS = {"prompt_injection", "retrieval"}


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
    }


def make_absorption_result(packet_id: str, status: str = "absorbed", reason: Optional[str] = None) -> Dict[str, Any]:
    result = {
        "packet_ref": packet_id,
        "status": status,
    }
    if reason:
        result["reason"] = reason
    if status == "absorbed":
        result["effect_summary"] = {
            "note": "network dual-end demo absorption",
            "metrics": {"baseline_sr": 0.6, "regression_sr": 0.85, "declared_N": 3},
        }
    return result


class A2AHandler(BaseHTTPRequestHandler):
    role: str = "receiver"
    peer_url: Optional[str] = None
    sign_key: Optional[str] = None
    received: list = []

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

        print(f"[{self.role}] received: {json.dumps(msg, ensure_ascii=False)[:180]}...")
        self.received.append(msg)

        response: Dict[str, Any] = {"ack": True}

        if "constraints" in msg:
            response["type"] = "profile_ack"
        elif "id" in msg and "absorption" in msg:
            method = msg.get("absorption", {}).get("method")
            if method not in MUST_METHODS and method not in ("local_adapter_hint", "trajectory_replay"):
                result = make_absorption_result(msg["id"], "rejected", f"unsupported method: {method}")
            else:
                result = make_absorption_result(msg["id"], "absorbed")
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
