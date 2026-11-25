#!/usr/bin/env python3
"""
Minimal network dual-endpoint reference (non-normative).

Two processes exchange Agent Profile, Experience Packet, Absorption Result
over HTTP using only the Python standard library.

Core helpers (canonical JSON, HMAC, replay window, version rejection,
legal rejected Absorption Results) live in the installable package
``ai_oriented_a2a``, and the HTTP plumbing (client, request handler) lives in
``ai_oriented_a2a.transport``. This file remains the HTTP dual-endpoint demo:
integrators should use ``ai_oriented_a2a.transport`` rather than copy it.

For supported absorption methods the receiver runs the local regression +
isolation N path from minimal_dual_end.Receiver.absorb (experiment-path gate,
stricter than the wire hard gate). Unsupported methods are rejected
explicitly. Packet content is untrusted data (SPEC §2.1): a packet, or a
negotiation message whose offered_packet / conditional_packet, matches a
forbidden directive pattern (docs/DATA_POLICY.md) is rejected with
content_policy: before the method check, and is never absorbed. Negotiation
messages (SPEC §3) then pass the NegotiationGuard: more than 3 rounds for one
negotiation_id (DEFAULT_MAX_NEGOTIATION_ROUNDS) get negotiation_limit:, and a
propose/counter whose target_constraint is one of the receiver's
core_constraints (--core-constraint, repeatable) gets immutable_constraint:.
Everything else still gets negotiation_unsupported:. On the wire,
effect_summary.note is MUST when status is absorbed (SPEC §3.1); full
metrics remain OPTIONAL. The receiver converts a missing
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
import hashlib
import json
import random
import sys
import threading
import time
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple, Union

_EXAMPLES = Path(__file__).resolve().parent
_REPO = _EXAMPLES.parent
# Allow `python3 examples/network_dual_end.py` without installing the package.
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
# Several names are re-exported for tests and older callers that import them from here.
from ai_oriented_a2a import (  # noqa: E402,F401
    RESERVED_PACKET_REF,
    SUPPORTED_MAJOR_MINOR,
    VERSION_PATTERN,
    ReplayCache,
    canonical,
    check_incoming,
    check_replay,
    new_id,
    RESERVED_PACKET_REF,
    packet_ref_of,
    parse_timestamp,
    reject_result,
    sign_message,
    supported_version,
    verify_message,
    version_rejection,
)
from ai_oriented_a2a import NegotiationGuard, content_policy_rejection, transport  # noqa: E402
from ai_oriented_a2a.transport import A2ARequestHandler, guard_result  # noqa: E402
from minimal_dual_end import MUST_METHODS, Receiver  # noqa: E402


def stable_seed(packet_id: Any) -> int:
    """Seed for the simulated regression: first 8 bytes of sha256(id).

    Python's built-in ``hash()`` of a string changes per process, so the same
    packet could be absorbed in one run and rejected in the next.
    """
    return int.from_bytes(hashlib.sha256(str(packet_id).encode("utf-8")).digest()[:8], "big")


NEGOTIATION_UNSUPPORTED_REASON = (
    "negotiation_unsupported: this demo receiver does not implement negotiation; "
    "nested packets are not processed"
)
DEFAULT_REPLAY_WINDOW_SECONDS = 10 * 60  # 10 minutes, per SECURITY.md suggestion
CLIENT_TIMEOUT_SECONDS = 30  # TRANSPORT.md: library SHOULD use >= 30s


def make_profile() -> Dict[str, Any]:
    profile: Dict[str, Any] = {
        "constraints": ["web_search allowed", "no parameter_delta"],
        "improvement_prefs": ["failure_mode", "tool_trajectory"],
        "shareable_types": ["failure_mode"],
        "interest_tags": ["timeout_recovery"],
        "nonce": new_id("nonce"),
        "timestamp": int(time.time()),
    }
    if A2AHandler.core_constraints:
        profile["core_constraints"] = list(A2AHandler.core_constraints)
    return profile


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


def negotiation_rejection(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Legal rejected Absorption Result for a negotiation message (SPEC §3.1).

    ``packet_ref`` is ``offered_packet.id`` when usable, else ``"unknown"``.
    """
    offered = msg.get("offered_packet")
    ref = offered.get("id") if isinstance(offered, dict) else None
    if not (isinstance(ref, str) and ref.strip()):
        ref = RESERVED_PACKET_REF
    return {"packet_ref": ref, "status": "rejected", "reason": NEGOTIATION_UNSUPPORTED_REASON}


def guard_reserved_ref(result: Dict[str, Any]) -> Dict[str, Any]:
    """Refuse to emit status=absorbed with the reserved packet_ref."""
    if result.get("status") == "absorbed" and result.get("packet_ref") == RESERVED_PACKET_REF:
        return reject_result(
            result,
            "reserved packet_ref 'unknown' cannot be used for successful absorption",
        )
    return result


class A2AHandler(A2ARequestHandler):
    """Demo receiver built on the package handler (ai_oriented_a2a.transport).

    The package handler does the path check, invalid-JSON reply,
    check_incoming (version, optional signature, optional replay window),
    the reply hard gate, and signed replies. This subclass only decides the
    reply for messages that passed those checks.
    """

    role: str = "receiver"
    peer_url: Optional[str] = None
    sign_key: Optional[str] = None
    replay_window: Optional[float] = None  # seconds; None = replay checks off
    received: list = []
    receiver: Receiver = Receiver(isolation_n=3, regression_drop_threshold=0.10)
    # Process-level replay cache (set + lock) for the demo; per-peer would key
    # by identity/fingerprint. A bare set assigned here is wrapped on first use.
    seen_nonces: Union[ReplayCache, Set[Tuple[str, str]]] = ReplayCache()
    # SPEC §3: core constraints are set by the host only (never by a message).
    core_constraints: list = []
    # Round cap per negotiation_id (default 3) and optional per-peer rate
    # limit (off here). Replace with NegotiationGuard(max_rounds=...,
    # peer_rate_limit=...) to tune; run_server installs a fresh one.
    negotiation_guard: NegotiationGuard = NegotiationGuard()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.role}] {self.address_string()} - {format % args}")

    def handle_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        print(f"[{self.role}] received: {json.dumps(msg, ensure_ascii=False)[:180]}...")
        self.received.append(msg)

        response: Dict[str, Any] = {"ack": True}
        # A packet is recognised by ``absorption`` alone: requiring ``id``
        # here let a packet with a blank or missing id skip the content
        # check entirely and answer with a bare ack (audit P0-3).
        is_packet = "absorption" in msg
        # SPEC §2.1 (c): directive-like content in a packet or in a nested
        # packet of a negotiation message is rejected, never absorbed.
        policy = (
            content_policy_rejection(msg) if is_packet or "proposal_id" in msg else None
        )
        # SPEC §3: every negotiation message counts as a round for its
        # negotiation_id, even one rejected for content_policy below. The
        # guard's own order is negotiation_limit, then immutable_constraint.
        limited = (
            self.negotiation_guard.check(msg, self.core_constraints, peer=self.client_address[0])
            if "proposal_id" in msg
            else None
        )

        if policy is not None:
            response = guard_result(policy)
        elif "proposal_id" in msg:
            if limited is not None:
                response = guard_result(limited)
            else:
                # Negotiation (propose / accept / reject / counter): not
                # implemented here, so reject with a legal Absorption Result
                # instead of a bare ack (KNOWN_GAPS fix in 0.3.3).
                response = negotiation_rejection(msg)
        elif "constraints" in msg:
            response["type"] = "profile_ack"
        elif is_packet:
            ref = packet_ref_of(msg)
            method = msg.get("absorption", {}).get("method")
            if ref == RESERVED_PACKET_REF:
                # SPEC: "unknown" MUST NOT be the packet_ref of a successful
                # absorption, so an unattributable packet is rejected, not
                # absorbed and not acked.
                result = reject_result(
                    msg, "missing_id: a packet with no usable id cannot be absorbed or attributed"
                )
            elif method not in MUST_METHODS and method not in ("local_adapter_hint", "trajectory_replay"):
                result = reject_result(msg, f"unsupported method: {method}")
            else:
                packet = dict(msg, id=ref)
                rng = random.Random(stable_seed(ref))
                result = self.receiver.absorb(packet, rng)
            response = guard_result(guard_reserved_ref(result))
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

        return response

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
        try:
            transport.send_message(
                self.peer_url,
                payload,
                timeout=CLIENT_TIMEOUT_SECONDS,
                sign_key=self.sign_key,
            )
            print(f"[{self.role}] pushed result")
        except transport.TransportError as e:
            print(f"[{self.role}] push failed: {e}")


def run_server(
    port: int,
    role: str,
    peer_url: Optional[str],
    sign_key: Optional[str],
    replay_window: Optional[float] = None,
    core_constraints: Optional[list] = None,
) -> None:
    A2AHandler.role = role
    A2AHandler.peer_url = peer_url
    A2AHandler.sign_key = sign_key
    A2AHandler.replay_window = replay_window
    A2AHandler.seen_nonces = ReplayCache()
    A2AHandler.core_constraints = list(core_constraints or [])
    A2AHandler.negotiation_guard = NegotiationGuard()
    server = HTTPServer(("127.0.0.1", port), A2AHandler)
    window_desc = f"{replay_window}s" if replay_window is not None else "off"
    print(
        f"[{role}] listening on http://127.0.0.1:{port}/a2a/message "
        f"(sign={'on' if sign_key else 'off'}, replay={window_desc})"
    )
    server.serve_forever()


def send_message(url: str, payload: Dict[str, Any], sign_key: Optional[str] = None) -> Dict[str, Any]:
    """DEPRECATED demo wrapper; will be removed in a future release.

    It keeps the old positional ``sign_key`` argument for existing callers
    (network_multi_node_demo.py and tests). New code MUST NOT copy it: use
    ``ai_oriented_a2a.transport.send_message(url, message, timeout=..., sign_key=...)``
    with keyword arguments instead.
    """
    return transport.send_message(url, payload, timeout=CLIENT_TIMEOUT_SECONDS, sign_key=sign_key)


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
    parser.add_argument(
        "--core-constraint",
        action="append",
        default=[],
        metavar="TEXT",
        help=(
            "a core constraint (SPEC §3, repeatable): a propose or counter whose "
            "target_constraint equals it is rejected with immutable_constraint:"
        ),
    )
    args = parser.parse_args()

    if args.role == "receiver":
        run_server(args.port, "receiver", args.peer, args.sign_key, args.replay_window, args.core_constraint)
    else:
        threading.Thread(
            target=run_server,
            args=(args.port, "sender", args.peer, args.sign_key, args.replay_window, args.core_constraint),
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
