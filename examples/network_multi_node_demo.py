#!/usr/bin/env python3
"""
Minimal multi-node network discovery demo (non-normative).

Starts a small set of local HTTP servers that speak the same /a2a/message
endpoint as network_dual_end.py. Nodes advertise interest_tags in their
Agent Profile. A sender discovers a matching peer by querying known endpoints
and then sends a distilled Experience Packet over real HTTP.

No new wire messages. Discovery is just ordinary profile exchange + local
matching on interest_tags (the prose in SPEC.md §4). Absorption uses the
same hard-gated path as network_dual_end.

Run from repo root:
  python3 examples/network_multi_node_demo.py
"""

from __future__ import annotations

import json
import random
import threading
import time
from http.server import HTTPServer
from typing import Any, Dict, List, Optional

from network_dual_end import A2AHandler, make_packet, make_profile, send_message


def start_node(port: int, tags: List[str]) -> HTTPServer:
    handler = type("NodeHandler", (A2AHandler,), {"role": f"node-{port}"})
    # Each node gets its own Receiver instance (class var is shared by default;
    # override per subclass if needed). For demo we share the process-level one
    # and rely on packet id isolation inside absorb.
    server = HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # Advertise tags via a one-shot profile post to self is unnecessary;
    # the demo just records them locally and matches before sending.
    print(f"[demo] node listening on http://127.0.0.1:{port} tags={tags}")
    return server


def discover_and_send(ports: List[int], target_tags: List[str]) -> Optional[Dict[str, Any]]:
    """Query each known endpoint for its profile; pick first tag overlap; send packet."""
    for port in ports:
        url = f"http://127.0.0.1:{port}"
        try:
            # Send a lightweight profile probe (the handler returns profile_ack).
            ack = send_message(url, make_profile())
            print(f"[demo] probed {url} -> {ack.get('type')}")
        except Exception as e:
            print(f"[demo] probe {url} failed: {e}")
            continue
        # In a real gossip the peer would have returned its interest_tags.
        # Here we simulate the match by assuming the node was started with
        # overlapping tags (demo only). If we want true remote tags we would
        # extend the profile_ack to echo them; left optional.
        if set(target_tags) & {"timeout_recovery", "failure_recovery"}:
            pkt = make_packet()
            print(f"[demo] sending distilled packet {pkt['id']} to {url}")
            result = send_message(url, pkt)
            return result
    return None


def main() -> None:
    ports = [8101, 8102, 8103]
    tags = ["timeout_recovery", "backoff", "web_search"]
    servers = [start_node(p, tags) for p in ports]
    time.sleep(0.4)

    result = discover_and_send(ports, ["timeout_recovery"])
    if result:
        print("[demo] absorption result:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        status = result.get("status")
        has_summary = "effect_summary" in result
        print(f"[demo] status={status} has_effect_summary={has_summary}")
    else:
        print("[demo] no matching peer found")

    print("[demo] done. Nodes will exit with the process.")
    # Keep briefly so logs flush.
    time.sleep(0.5)
    for s in servers:
        s.shutdown()


if __name__ == "__main__":
    main()
